#!/usr/bin/env bash
set -euo pipefail

# ./turn_off_service.sh --turn-off
# Will turn off everything except the ALB pretty much
# or
# ./turn_off_service.sh --minimal-cost
# Will try to get rid of everything pretty much. Should come out to almost $0 cost after this.

########################################
# Config – edit if you ever rename stuff
########################################
REGION="ap-southeast-2"
APP="liliumshare"
CLUSTER="${APP}-cluster"
SERVICE="${APP}-svc"
ALB_NAME="${APP}-alb"
TG_NAME="${APP}-tg"
LOG_GROUP="/ecs/${APP}"

usage() {
  cat <<EOF
Usage:
  $0 --turn-off        Scale ECS service to 0 tasks (keeps ALB/TG/etc)
  $0 --minimal-cost    Scale ECS to 0 AND delete ALB/TG/etc to stop hourly spend

After --minimal-cost, run build_project_aws.sh to bring it all back.
EOF
  exit 1
}

if [[ $# -ne 1 ]]; then
  usage
fi

MODE="$1"
if [[ "$MODE" != "--turn-off" && "$MODE" != "--minimal-cost" ]]; then
  usage
fi

say() { printf "\n==> %s\n" "$*"; }
warn() { printf "\n[WARN] %s\n" "$*"; }

########################################
# helper: scale ECS service
########################################
scale_service_to_zero() {
  say "Scaling ECS service ${SERVICE} on cluster ${CLUSTER} to desired-count=0"
  aws ecs update-service \
    --region "$REGION" \
    --cluster "$CLUSTER" \
    --service "$SERVICE" \
    --desired-count 0 >/dev/null || warn "update-service failed (maybe service missing?)"

  say "Waiting for service to stabilize at 0 tasks…"
  aws ecs wait services-stable \
    --region "$REGION" \
    --cluster "$CLUSTER" \
    --services "$SERVICE" || warn "services-stable waiter failed (may be already at 0)"
}

########################################
# helper: nuke ALB + listeners
########################################
delete_alb_and_tg() {
  say "Looking up ALB ARN for ${ALB_NAME}"
  local alb_arn
  alb_arn="$(aws elbv2 describe-load-balancers \
    --region "$REGION" \
    --names "$ALB_NAME" \
    --query 'LoadBalancers[0].LoadBalancerArn' \
    --output text 2>/dev/null || true)"

  if [[ -z "$alb_arn" || "$alb_arn" == "None" ]]; then
    warn "No ALB named ${ALB_NAME} found. Skipping ALB cleanup."
  else
    say "Deleting listeners on ALB ${ALB_NAME}"
    local listeners
    listeners="$(aws elbv2 describe-listeners \
      --region "$REGION" \
      --load-balancer-arn "$alb_arn" \
      --query 'Listeners[].ListenerArn' \
      --output text 2>/dev/null || true)"
    if [[ -n "$listeners" ]]; then
      for L in $listeners; do
        say " - deleting listener $L"
        aws elbv2 delete-listener \
          --region "$REGION" \
          --listener-arn "$L" >/dev/null || warn "could not delete listener $L"
      done
    fi

    say "Deleting ALB ${ALB_NAME}"
    aws elbv2 delete-load-balancer \
      --region "$REGION" \
      --load-balancer-arn "$alb_arn" >/dev/null || warn "could not delete ALB $alb_arn"

    say "Waiting for ALB to fully delete (this can take a bit)…"
    # We'll poll until describe-load-balancers fails for that ARN.
    for i in {1..60}; do
      if aws elbv2 describe-load-balancers \
          --region "$REGION" \
          --load-balancer-arns "$alb_arn" >/dev/null 2>&1; then
        sleep 5
      else
        break
      fi
    done
  fi

  say "Looking up Target Group ARN for ${TG_NAME}"
  local tg_arn
  tg_arn="$(aws elbv2 describe-target-groups \
    --region "$REGION" \
    --names "$TG_NAME" \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text 2>/dev/null || true)"

  if [[ -z "$tg_arn" || "$tg_arn" == "None" ]]; then
    warn "No target group ${TG_NAME} found. Skipping TG cleanup."
  else
    say "Deleting Target Group ${TG_NAME}"
    aws elbv2 delete-target-group \
      --region "$REGION" \
      --target-group-arn "$tg_arn" >/dev/null || warn "could not delete TG $tg_arn"
  fi
}

########################################
# helper: reduce CloudWatch log retention
########################################
tune_logs() {
  say "Setting CloudWatch Logs retention for ${LOG_GROUP} to 1 day"
  aws logs put-retention-policy \
    --region "$REGION" \
    --log-group-name "$LOG_GROUP" \
    --retention-in-days 1 >/dev/null || warn "could not update log retention"
}

########################################
# MAIN
########################################
if [[ "$MODE" == "--turn-off" ]]; then
  say "Mode: TURN OFF (no tasks running, ALB left intact)"
  scale_service_to_zero
  tune_logs
  say "Done. Fargate tasks are stopped. ALB still exists (you will still pay a small hourly ALB cost)."
  exit 0
fi

if [[ "$MODE" == "--minimal-cost" ]]; then
  say "Mode: MINIMAL COST (try to get you basically $0)"
  scale_service_to_zero
  delete_alb_and_tg
  tune_logs
  say "Done. No running tasks, no ALB, no target group. Remaining charges should just be pennies for storage (ECR images, Secrets Manager, etc.)."
  exit 0
fi

