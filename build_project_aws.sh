#!/usr/bin/env bash
set -Eeuo pipefail

############################################
# Config — adjust if needed
############################################
export AWS_REGION="ap-southeast-2"
export AWS_DEFAULT_REGION="$AWS_REGION"

APP_NAME="liliumshare"
ECR_REPO="${APP_NAME}-backend"
IMAGE_TAG="latest"

CONTAINER_PORT=18080       # container listens here
PUBLIC_PORT=80             # ALB listener port

LOG_GROUP="/ecs/${APP_NAME}"
CLUSTER_NAME="${APP_NAME}-cluster"
SERVICE_NAME="${APP_NAME}-svc"
TASK_FAMILY="${APP_NAME}-backend"
MIGRATE_FAMILY="${APP_NAME}-migrate"   # one-off migration task def

DB_SECRET_NAME="liliumshare/prod/DATABASE_URL"   # must exist in $AWS_REGION

############################################
# Helpers
############################################
say(){ printf "\n==> %s\n" "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }
join_by_comma(){ local IFS=,; echo "$*"; }

need() { command -v "$1" >/dev/null 2>&1 || die "Missing: $1"; }

wait_task_stop() {
  local cluster="$1" task="$2" limit="${3:-40}"   # 40 * 10s = ~6m40s
  local i=0 last=""
  while :; do
    local st; st="$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task" \
      --query 'tasks[0].lastStatus' --output text 2>/dev/null || true)"
    [[ -n "$st" ]] && last="$st"
    if [[ "$st" == "STOPPED" ]]; then return 0; fi
    i=$((i+1)); if (( i>=limit )); then
      echo "(timeout waiting for STOPPED; lastStatus=${last:-unknown})"
      return 1
    fi
    sleep 10
  done
}

task_exit_code() {
  local cluster="$1" task="$2"
  aws ecs describe-tasks --cluster "$cluster" --tasks "$task" \
    --query 'tasks[0].containers[0].exitCode' --output text 2>/dev/null || echo "None"
}

tail_logs() {
  local group="$1" limit="${2:-80}"
  aws logs filter-log-events --log-group-name "$group" --limit "$limit" \
    --query 'events[].{t:timestamp,m:message}' --output table 2>/dev/null || true
}

############################################
# Start
############################################
need aws; need docker; need jq

say "Region: $AWS_REGION"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
say "Account: $AWS_ACCOUNT_ID"

############################################
# VPC + Subnets (default VPC)
############################################
VPC_ID="$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query 'Vpcs[0].VpcId' --output text || true)"
[[ -z "${VPC_ID:-}" || "$VPC_ID" == "None" ]] && die "No default VPC found in $AWS_REGION"
say "VPC: $VPC_ID"

# Try public subnets, fallback to any
mapfile -t ALL_PUBLIC_SUBNETS < <(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[?MapPublicIpOnLaunch==`true`].SubnetId' --output text | tr '\t' '\n' | sed '/^$/d')
if (( ${#ALL_PUBLIC_SUBNETS[@]} < 2 )); then
  mapfile -t ALL_PUBLIC_SUBNETS < <(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --query 'Subnets[].SubnetId' --output text | tr '\t' '\n' | sed '/^$/d')
fi
(( ${#ALL_PUBLIC_SUBNETS[@]} >= 2 )) || die "Need at least 2 subnets in VPC $VPC_ID"
SUBNETS=("${ALL_PUBLIC_SUBNETS[@]:0:3}")
say "Subnets: ${SUBNETS[*]}"

############################################
# ECR repo + Docker push
############################################
say "Logging in to ECR"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

say "Ensuring ECR repo: $ECR_REPO"
aws ecr describe-repositories --repository-names "$ECR_REPO" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$ECR_REPO" >/dev/null

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

say "Building Docker image"
docker build -t "${APP_NAME}-backend:${IMAGE_TAG}" ./backend

say "Tagging & pushing ${ECR_URI}:${IMAGE_TAG}"
docker tag  "${APP_NAME}-backend:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:${IMAGE_TAG}"

############################################
# IAM: ECS task execution role
############################################
ROLE_NAME="${APP_NAME}-ecsTaskExecutionRole"
say "Creating/using IAM role $ROLE_NAME"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite >/dev/null
fi
EXEC_ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)"

############################################
# Security Groups
############################################
say "Ensuring security groups"
# ALB SG
ALB_SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=${APP_NAME}-alb-sg" \
  --query 'SecurityGroups[0].GroupId' --output text || true)"
if [[ -z "${ALB_SG_ID:-}" || "$ALB_SG_ID" == "None" ]]; then
  ALB_SG_ID="$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-alb-sg" --description "ALB SG for ${APP_NAME}" --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)"
fi
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG_ID" \
  --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null 2>&1 || true

# ECS SG
ECS_SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=${APP_NAME}-ecs-sg" \
  --query 'SecurityGroups[0].GroupId' --output text || true)"
if [[ -z "${ECS_SG_ID:-}" || "$ECS_SG_ID" == "None" ]]; then
  ECS_SG_ID="$(aws ec2 create-security-group \
    --group-name "${APP_NAME}-ecs-sg" --description "ECS SG for ${APP_NAME}" --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)"
fi
aws ec2 authorize-security-group-ingress \
  --group-id "$ECS_SG_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=${CONTAINER_PORT},ToPort=${CONTAINER_PORT},UserIdGroupPairs=[{GroupId=${ALB_SG_ID}}]" >/dev/null 2>&1 || true

############################################
# CloudWatch Logs
############################################
aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" \
  --query 'logGroups[?logGroupName==`'"$LOG_GROUP"'`].logGroupName' --output text | grep -q "$LOG_GROUP" \
  || aws logs create-log-group --log-group-name "$LOG_GROUP"

############################################
# Target Group + ALB + Listener
############################################
say "Creating/using Target Group"
TG_ARN="$(aws elbv2 describe-target-groups --names "${APP_NAME}-tg" \
  --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)"
if [[ -z "${TG_ARN:-}" || "$TG_ARN" == "None" ]]; then
  TG_ARN="$(aws elbv2 create-target-group \
    --name "${APP_NAME}-tg" \
    --protocol HTTP --port "$CONTAINER_PORT" \
    --vpc-id "$VPC_ID" --target-type ip \
    --health-check-path "/health" \
    --health-check-interval-seconds 15 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --query 'TargetGroups[0].TargetGroupArn' --output text)"
fi

say "Creating/using ALB"
ALB_ARN="$(aws elbv2 describe-load-balancers --names "${APP_NAME}-alb" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)"
if [[ -z "${ALB_ARN:-}" || "$ALB_ARN" == "None" ]]; then
  # shellcheck disable=SC2206
  ALB_SUBNET_ARGS=(${SUBNETS[*]})
  ALB_ARN="$(aws elbv2 create-load-balancer \
    --name "${APP_NAME}-alb" \
    --type application --scheme internet-facing \
    --security-groups "$ALB_SG_ID" \
    --subnets "${ALB_SUBNET_ARGS[@]}" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)"
fi
ALB_DNS="$(aws elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' --output text)"
say "ALB DNS: http://$ALB_DNS"

say "Creating/using Listener :${PUBLIC_PORT}"
LS_ARN="$(aws elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" \
  --query 'Listeners[?Port==`'"$PUBLIC_PORT"'`].ListenerArn' --output text || true)"
if [[ -z "${LS_ARN:-}" || "$LS_ARN" == "None" ]]; then
  LS_ARN="$(aws elbv2 create-listener \
    --load-balancer-arn "$ALB_ARN" --protocol HTTP --port "$PUBLIC_PORT" \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" \
    --query 'Listeners[0].ListenerArn' --output text)"
else
  aws elbv2 modify-listener --listener-arn "$LS_ARN" \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
fi

############################################
# ECS cluster
############################################
say "Creating/using ECS cluster"
aws ecs describe-clusters --clusters "$CLUSTER_NAME" \
  --query 'clusters[0].status' --output text 2>/dev/null | grep -q ACTIVE \
  || aws ecs create-cluster --cluster-name "$CLUSTER_NAME" >/dev/null

############################################
# Secrets Manager (DB URL)
############################################
say "Resolving DB secret"
DB_SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$DB_SECRET_NAME" --query 'ARN' --output text 2>/dev/null || true)"
[[ -z "${DB_SECRET_ARN:-}" || "$DB_SECRET_ARN" == "None" ]] && die "Secret '$DB_SECRET_NAME' not found."

############################################
# Register MIGRATION task definition
# (force entryPoint/command to run migrate and exit)
############################################
say "Registering one-off MIGRATION task definition"
MIG_TD_JSON="$(cat <<EOF
{
  "family": "${MIGRATE_FAMILY}",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "${EXEC_ROLE_ARN}",
  "containerDefinitions": [
    {
      "name": "${APP_NAME}-container",
      "image": "${ECR_URI}:${IMAGE_TAG}",
      "entryPoint": ["bash","-lc"],
      "command": ["NODE_TLS_REJECT_UNAUTHORIZED=0 PGSSLMODE=no-verify node src/migrate.js"],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "${DB_SECRET_ARN}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "${LOG_GROUP}",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "${APP_NAME}-migrate"
        }
      },
      "essential": true
    }
  ]
}
EOF
)"
MIGRATE_TD_ARN="$(aws ecs register-task-definition --cli-input-json "$MIG_TD_JSON" \
  --query 'taskDefinition.taskDefinitionArn' --output text)"
say "Migrate TaskDef: $MIGRATE_TD_ARN"

############################################
# Run migration task (and wait for STOPPED)
############################################
say "Running one-off migration task…"
SUBNET_CSL="$(join_by_comma "${SUBNETS[@]}")"
TASK_ARN="$(aws ecs run-task \
  --cluster "$CLUSTER_NAME" \
  --launch-type FARGATE \
  --task-definition "$MIGRATE_TD_ARN" \
  --count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_CSL],securityGroups=[$ECS_SG_ID],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' --output text)"
echo "Started migration task: $TASK_ARN"

if ! wait_task_stop "$CLUSTER_NAME" "$TASK_ARN" 48; then
  echo "Migration task did not stop in time."
  echo "Recent logs from ${LOG_GROUP}:"
  tail_logs "$LOG_GROUP" 120
  die "Migration timed out (task still RUNNING?)"
fi

MIG_RC="$(task_exit_code "$CLUSTER_NAME" "$TASK_ARN")"
echo "Migration exit code: $MIG_RC"
if [[ "$MIG_RC" != "0" ]]; then
  echo "Migration failed — last log lines:"
  tail_logs "$LOG_GROUP" 120
  die "Migration failed (exit $MIG_RC)"
fi

############################################
# Normal runtime task definition (service)
############################################
say "Registering RUNTIME task definition"
RUNTIME_TD_JSON="$(cat <<EOF
{
  "family": "${TASK_FAMILY}",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "${EXEC_ROLE_ARN}",
  "containerDefinitions": [
    {
      "name": "${APP_NAME}-container",
      "image": "${ECR_URI}:${IMAGE_TAG}",
      "portMappings": [{ "containerPort": ${CONTAINER_PORT}, "protocol": "tcp" }],
      "environment": [
        { "name": "PORT", "value": "${CONTAINER_PORT}" }
      ],
      "secrets": [
        { "name": "DATABASE_URL", "valueFrom": "${DB_SECRET_ARN}" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "${LOG_GROUP}",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "${APP_NAME}"
        }
      },
      "essential": true
    }
  ]
}
EOF
)"
TD_ARN="$(aws ecs register-task-definition --cli-input-json "$RUNTIME_TD_JSON" \
  --query 'taskDefinition.taskDefinitionArn' --output text)"
say "Runtime TaskDef: $TD_ARN"

############################################
# Service (create or update)
############################################
say "Creating/updating ECS service"
EXISTS="$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" \
  --query 'services[0].status' --output text 2>/dev/null || true)"

if [[ "${EXISTS:-}" == "ACTIVE" ]]; then
  aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SERVICE_NAME" \
    --task-definition "$TD_ARN" >/dev/null
else
  aws ecs create-service \
    --cluster "$CLUSTER_NAME" \
    --service-name "$SERVICE_NAME" \
    --task-definition "$TD_ARN" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_CSL],securityGroups=[$ECS_SG_ID],assignPublicIp=ENABLED}" \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=${APP_NAME}-container,containerPort=${CONTAINER_PORT}" >/dev/null
fi

say "Waiting for service to stabilize…"
set +e
aws ecs wait services-stable --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME"
WAITER_RC=$?
set -e

echo

# Always capture useful state
echo "— ECS service (deployments) —"
aws ecs describe-services \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --query 'services[0].{
    Status:status,
    Desired:desiredCount,
    Running:runningCount,
    Pending:pendingCount,
    TaskDef:taskDefinition,
    Deployments:deployments[].{Id:id,Status:status,RolloutState:rolloutState,Running:runningCount,Pending:pendingCount,Desired:desiredCount,Updated:updatedAt}
  }' --output json

echo
echo "— Target Group attributes & health —"
aws elbv2 describe-target-group-attributes --target-group-arn "$TG_ARN" \
  --query 'Attributes[?Key==`deregistration_delay.timeout_seconds` || Key==`load_balancing.algorithm.type`]' --output table || true
aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
  --query 'TargetHealthDescriptions[].{Target:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason}' \
  --output table

# Health probe to ALB (works even if waiter timed out due to long draining)
ALB_DNS="$(aws elbv2 describe-load-balancers --names "${APP_NAME}-alb" --query 'LoadBalancers[0].DNSName' --output text)"
echo
echo "— Probing ALB /health —"
HEALTH_BODY="$(curl -sS -m 4 "http://${ALB_DNS}/health" || true)"
echo "GET http://${ALB_DNS}/health -> ${HEALTH_BODY}"

# Count healthy targets
HEALTHY_CT="$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
  --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' --output text 2>/dev/null || echo 0)"

# Decide success if waiter failed but app is demonstrably healthy
if [[ $WAITER_RC -ne 0 ]]; then
  echo
  echo "!!! ServicesStable waiter FAILED. Evaluating real health…"
  SRV_JSON="$(aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" --output json)"
  RUNNING="$(echo "$SRV_JSON" | jq -r '.services[0].runningCount // 0')"
  DESIRED="$(echo "$SRV_JSON" | jq -r '.services[0].desiredCount // 0')"

  if [[ "$RUNNING" -ge 1 && "$RUNNING" -eq "$DESIRED" && "$HEALTHY_CT" -ge 1 && "$HEALTH_BODY" == *'"ok":true'* ]]; then
    echo "✔ Service is effectively healthy (running=$RUNNING, healthy_targets=$HEALTHY_CT, /health ok). Proceeding."
  else
    echo "✖ Waiter failed and effective health not met. Collecting logs and exiting 1…"
    echo
    echo "— Recent app logs —"
    aws logs filter-log-events --log-group-name "$LOG_GROUP" --limit 120 \
      --query 'events[].{t:timestamp,m:message,stream:logStreamName}' --output table 2>/dev/null || true
    exit 1
  fi
fi

cat <<TIP

==> Deployed.
ALB URL:  http://${ALB_DNS}

Quick checks:
  curl -fsS http://${ALB_DNS}/health
  curl -fsS http://${ALB_DNS}/api/debug/users

If the waiter keeps timing out:
  • It’s usually just long ELB deregistration_delay (default 300s) keeping an old task draining.
  • You can lower it: aws elbv2 modify-target-group-attributes --target-group-arn "$TG_ARN" --attributes Key=deregistration_delay.timeout_seconds,Value=30
  • Or just rely on the /health probe + healthy TG targets (as this script now does).
TIP
