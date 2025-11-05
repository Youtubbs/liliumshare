#!/bin/bash

bash <<'EOF'
# ===== liliumshare deep network+connectivity dump =====
# One-shot script: prints ALB/ECS/RDS wiring, SG/NACL/Routes, recent logs, and runs curl tests.

set +e
REGION="ap-southeast-2"
export AWS_DEFAULT_REGION="$REGION"

# Known resources (adjust if you renamed anything)
CLUSTER_NAME="liliumshare-cluster"
SERVICE_NAME="liliumshare-svc"
TG_NAME="liliumshare-tg"
DB_ID="liliumshare-database-1"
# Fallback ALB DNS showed earlier (used for curl even if the API lookup fails)
# ALB_DNS_FALLBACK=""

line(){ printf '\n==== %s ====\n' "$*"; }
j(){ jq -r "${1:-.}" 2>/dev/null; }

line "Identify ALB by DNS (robust) + listeners/SG"
ALL_LB_JSON=$(aws elbv2 describe-load-balancers 2>/dev/null)
# Try to match by DNS fallback first, otherwise take the first internet-facing ALB in this VPC
ALB_JSON=$(echo "$ALL_LB_JSON" | jq --arg dns "$ALB_DNS_FALLBACK" '
  .LoadBalancers // [] | map(select(.Type=="application")) |
  (map(select(.DNSName==$dns)) + .) | .[0] // {}')
ALB_ARN=$(echo "$ALB_JSON" | j '.LoadBalancerArn // empty')
ALB_VPC=$(echo "$ALB_JSON" | j '.VpcId // empty')
ALB_DNS=$(echo "$ALB_JSON" | j '.DNSName // empty')
ALB_SGS=$(echo "$ALB_JSON" | j '.SecurityGroups[]?')
echo "ALB_ARN  = ${ALB_ARN:-<not found via API>}"
echo "ALB_VPC  = ${ALB_VPC:-unknown}"
echo "ALB_DNS  = ${ALB_DNS:-$ALB_DNS_FALLBACK}"
echo "ALB_SGs  = ${ALB_SGS:-<none>}"

if [ -n "$ALB_ARN" ]; then
  echo "-- Listeners (ports/actions) --"
  aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" \
    --query 'Listeners[].{Port:Port,Protocol:Protocol,DefaultActions:DefaultActions}' --output table
else
  echo "(Could not resolve ALB via API, continuing with DNS fallback for curl)…"
fi

line "Target Group config and target health"
TG_ARN=$(aws elbv2 describe-target-groups --names "$TG_NAME" --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null)
echo "TG_ARN   = ${TG_ARN:-<none>}"
if [ -n "$TG_ARN" ] && [ "$TG_ARN" != "None" ]; then
  aws elbv2 describe-target-groups --target-group-arns "$TG_ARN" \
    --query 'TargetGroups[0].{VpcId:VpcId,TargetType:TargetType,Port:Port,Protocol:Protocol,HCPath:HealthCheckPath,HCProt:HealthCheckProtocol,HCInt:HealthCheckIntervalSeconds,Timeout:HealthCheckTimeoutSeconds,Matcher:Matcher.HttpCode}' \
    --output table
  aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
    --query 'TargetHealthDescriptions[].{Target:Target.Id,AZ:Target.AvailabilityZone,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason,Desc:TargetHealth.Description}' \
    --output table
fi

line "ECS cluster/service/task + service events"
CLUSTER_ARN=$(aws ecs list-clusters --query "clusterArns[?contains(@, \`$CLUSTER_NAME\`)]|[0]" --output text 2>/dev/null)
SERVICE_ARN=$(aws ecs list-services --cluster "$CLUSTER_ARN" --query "serviceArns[?contains(@, \`$SERVICE_NAME\`)]|[0]" --output text 2>/dev/null)
echo "CLUSTER  = $CLUSTER_ARN"
echo "SERVICE  = $SERVICE_ARN"
aws ecs describe-services --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" \
  --query 'services[0].{TaskDef:taskDefinition,Desired:desiredCount,Running:runningCount,Pending:pendingCount,LB:loadBalancers,Net:networkConfiguration}' \
  --output table
echo "-- Recent ECS service events --"
aws ecs describe-services --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" \
  --query 'services[0].events[0:10].[createdAt,message]' --output table

TASK_ARN=$(aws ecs list-tasks --cluster "$CLUSTER_ARN" --service-name "$SERVICE_ARN" --desired-status RUNNING --query 'taskArns[0]' --output text 2>/dev/null)
TD_ARN=$(aws ecs describe-services --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" --query 'services[0].taskDefinition' --output text 2>/dev/null)
echo "TASK     = $TASK_ARN"
echo "TASKDEF  = $TD_ARN"
echo "-- Container defs (image/ports/env/secrets/logs) --"
aws ecs describe-task-definition --task-definition "$TD_ARN" \
  --query 'taskDefinition.containerDefinitions[].{Name:name,Image:image,PortMappings:portMappings,Env:environment,Secrets:secrets,LogCfg:logConfiguration}' \
  --output json | jq -r '. // []'

line "Task ENI + private IP (should appear in TG if TargetType=ip)"
ENI_ID=$(aws ecs describe-tasks --cluster "$CLUSTER_ARN" --tasks "$TASK_ARN" \
  --query 'tasks[0].attachments[].details[?name==`networkInterfaceId`].value|[0]' --output text 2>/dev/null)
aws ec2 describe-network-interfaces --network-interface-ids "$ENI_ID" \
  --query 'NetworkInterfaces[0].{PrivateIp:PrivateIpAddress,Subnet:SubnetId,AZ:AvailabilityZone,Vpc:VpcId,SGs:Groups[].GroupId}' \
  --output table

line "RDS instance, SGs, and subnets"
aws rds describe-db-instances --db-instance-identifier "$DB_ID" \
  --query 'DBInstances[0].{Engine:Engine,Ver:EngineVersion,Endpoint:Endpoint.Address,Port:Endpoint.Port,MultiAZ:MultiAZ,SubnetGroup:DBSubnetGroup.DBSubnetGroupName,VpcSecurityGroups:VpcSecurityGroups[].VpcSecurityGroupId,Subnets:DBSubnetGroup.Subnets[].SubnetIdentifier}' \
  --output table
DBHOST=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].Endpoint.Address' --output text 2>/dev/null)
DB_SGS=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].VpcSecurityGroups[].VpcSecurityGroupId' --output text 2>/dev/null)
echo "DBHOST   = $DBHOST"
echo "DB_SGs   = $DB_SGS"

line "Security Groups (ALB / ECS / DB)"
# ALB SGs
if [ -n "$ALB_SGS" ]; then
  for SG in $ALB_SGS; do
    echo "ALB_SG = $SG"
    aws ec2 describe-security-groups --group-ids "$SG" \
      --query '{Name:SecurityGroups[0].GroupName, Ingress:SecurityGroups[0].IpPermissions, Egress:SecurityGroups[0].IpPermissionsEgress}' \
      --output json | jq
  done
else
  echo "(No ALB SGs from API; you listed sg-066a9bdf5191fabdb and sg-04504e92a4e00a59a in the console.)"
  for SG in sg-066a9bdf5191fabdb sg-04504e92a4e00a59a; do
    aws ec2 describe-security-groups --group-ids "$SG" \
      --query '{Name:SecurityGroups[0].GroupName, Ingress:SecurityGroups[0].IpPermissions, Egress:SecurityGroups[0].IpPermissionsEgress}' \
      --output json | jq
  done
fi

# ECS SG
ECS_SG=$(aws ecs describe-services --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" --query 'services[0].networkConfiguration.awsvpcConfiguration.securityGroups[0]' --output text 2>/dev/null)
echo "ECS_SG  = $ECS_SG"
aws ec2 describe-security-groups --group-ids "$ECS_SG" \
  --query '{Name:SecurityGroups[0].GroupName, Ingress:SecurityGroups[0].IpPermissions, Egress:SecurityGroups[0].IpPermissionsEgress}' \
  --output json | jq

# DB SGs
for SG in $DB_SGS; do
  echo "DB_SG   = $SG"
  aws ec2 describe-security-groups --group-ids "$SG" \
    --query '{Name:SecurityGroups[0].GroupName, Ingress:SecurityGroups[0].IpPermissions, Egress:SecurityGroups[0].IpPermissionsEgress}' \
    --output json | jq
done

line "Subnets/NACLs/RouteTables for ECS + DB subnets"
ECS_SUBNETS=$(aws ecs describe-services --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" --query 'services[0].networkConfiguration.awsvpcConfiguration.subnets' --output text 2>/dev/null)
DB_SUBNETS=$(aws rds describe-db-instances --db-instance-identifier "$DB_ID" --query 'DBInstances[0].DBSubnetGroup.Subnets[].SubnetIdentifier' --output text 2>/dev/null)

show_net(){ 
  local S="$1"
  local AZ RT NACL
  AZ=$(aws ec2 describe-subnets --subnet-ids "$S" --query 'Subnets[0].AvailabilityZone' --output text)
  RT=$(aws ec2 describe-route-tables --filters "Name=association.subnet-id,Values=$S" --query 'RouteTables[0].RouteTableId' --output text)
  NACL=$(aws ec2 describe-network-acls --filters "Name=association.subnet-id,Values=$S" --query 'NetworkAcls[0].NetworkAclId' --output text)
  echo "--- Subnet $S (AZ=$AZ) RouteTable=$RT NACL=$NACL ---"
  echo "Routes:"
  aws ec2 describe-route-tables --route-table-ids "$RT" \
    --query 'RouteTables[0].Routes[].{DestCidr:DestinationCidrBlock,GatewayId:GatewayId,NatGatewayId:NatGatewayId,TransitGatewayId:TransitGatewayId,State:State}' \
    --output table
  echo "NACL:"
  aws ec2 describe-network-acls --network-acl-ids "$NACL" \
    --query 'NetworkAcls[0].Entries[].{RuleNumber:RuleNumber,Egress:Egress,Protocol:Protocol,Action:RuleAction,FromPort:PortRange.From,ToPort:PortRange.To,IPv4:CidrBlock,IPv6:Ipv6CidrBlock}' \
    --output table
}
echo "[ECS subnets]"
for S in $ECS_SUBNETS; do show_net "$S"; done
echo "[DB subnets]"
for S in $DB_SUBNETS; do show_net "$S"; done

line "CloudWatch Logs: last 50 events for /ecs/liliumshare and /ecs/liliumshare-backend"
for LG in /ecs/liliumshare /ecs/liliumshare-backend; do
  echo "--- $LG ---"
  aws logs filter-log-events --log-group-name "$LG" --limit 50 \
    --query 'events[].{t:timestamp,m:message,stream:logStreamName}' --output table 2>/dev/null
done

line "Connectivity tests"
echo "-- From YOUR machine to ALB (health + app path) --"
ALB_HOST="${ALB_DNS:-$ALB_DNS_FALLBACK}"
echo "ALB_HOST=$ALB_HOST"
curl -sS -m 6 "http://$ALB_HOST/health" | sed 's/.*/GET \/health -> &/' || echo "GET /health -> (timeout)"
curl -sS -m 10 "http://$ALB_HOST/api/debug/users" | sed 's/.*/GET \/api\/debug\/users -> &/' || echo "GET /api/debug/users -> (timeout)"

echo "-- DB DNS from YOUR machine --"
getent hosts "$DBHOST" 2>/dev/null || host "$DBHOST" 2>/dev/null

echo "-- If ecs:execute-command is enabled, run in-container checks (otherwise this will no-op) --"
aws ecs execute-command \
  --cluster "$CLUSTER_ARN" \
  --task "$TASK_ARN" \
  --container "liliumshare-container" \
  --interactive \
  --command "bash -lc 'echo [container] curl localhost:18080/health; curl -fsS -m 4 http://127.0.0.1:18080/health || echo FAIL; echo; echo [container] nc -zvw3 $DBHOST 5432 || echo nc-fail'" \
  >/dev/null 2>&1 || echo "(ecs:execute-command not enabled or IAM missing) — skipping container-side probes."

line "Summary Hints"
cat <<'HINTS'
- Your TG is ip-target-type on port 18080 with /health checks. The unhealthy target should match the task ENI private IP.
- ALB SG should allow inbound 80/443 from 0.0.0.0/0. ECS SG should allow inbound 18080 FROM ALB SG. DB SG should allow 5432 FROM ECS SG.
- NACLs shown here are wide-open (allow all) — not the problem.
- If /health works but /api/* times out, suspect the app is blocking that route, or it’s hanging on DB. Check the pod/container logs above.
- To test DB from inside the task, enable ECS Exec on the service and rerun this script.
HINTS
EOF

