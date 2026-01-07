#!/bin/bash
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

# Script to burn the error budget by generating high request rate to Prometheus.
#
# This script generates HTTP requests to Prometheus to trigger the SLO defined in Sloth.
# The SLO monitors Prometheus request activity and triggers alerts when the request rate
# exceeds 1 req/s, thereby burning the error budget.
#
# Usage:
#   ./scripts/burn_error_budget.sh <model-name> [duration_seconds] [rate_per_second]
#
# Examples:
#   ./scripts/burn_error_budget.sh mymodel 300 5    # 5 req/s for 5 minutes
#   ./scripts/burn_error_budget.sh mymodel 600 10   # 10 req/s for 10 minutes
#   ./scripts/burn_error_budget.sh mymodel          # defaults: 300s at 5 req/s

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
DURATION=${2:-300}  # 5 minutes default
RATE=${3:-5.0}      # 5 req/s default

# Check arguments
if [ -z "$1" ]; then
    echo -e "${RED}Error: Model name required${NC}"
    echo "Usage: $0 <model-name> [duration_seconds] [rate_per_second]"
    echo ""
    echo "Examples:"
    echo "  $0 mymodel 300 5    # 5 req/s for 5 minutes"
    echo "  $0 mymodel 600 10   # 10 req/s for 10 minutes"
    exit 1
fi

MODEL_NAME=$1

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Error Budget Burn Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo "Model: $MODEL_NAME"
echo "Duration: ${DURATION}s"
echo "Target rate: ${RATE} req/s"
echo ""

# Find Prometheus application
echo -e "${YELLOW}Finding Prometheus...${NC}"
PROM_APP=$(juju status --model "$MODEL_NAME" --format=json | jq -r '.applications | keys[] | select(contains("prometheus"))' | head -1)

if [ -z "$PROM_APP" ]; then
    echo -e "${RED}Error: Prometheus application not found in model${NC}"
    exit 1
fi

echo -e "${GREEN}Found Prometheus app: $PROM_APP${NC}"

# Get Prometheus unit number
PROM_UNIT=$(juju status --model "$MODEL_NAME" --format=json | jq -r ".applications[\"$PROM_APP\"].units | keys[0]" | cut -d'/' -f2)
PROM_FQDN="${PROM_APP}-${PROM_UNIT}.${PROM_APP}-endpoints.${MODEL_NAME}.svc.cluster.local"
PROM_PORT=9090
PROM_URL="http://${PROM_FQDN}:${PROM_PORT}/api/v1/query?query=up"

echo -e "${GREEN}Prometheus URL: $PROM_URL${NC}"
echo ""

# Find a pod to execute from
echo -e "${YELLOW}Finding pod for kubectl exec...${NC}"
POD_NAME=$(kubectl get pods -n "$MODEL_NAME" -l app.kubernetes.io/name=sloth -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$POD_NAME" ]; then
    # Fallback to any pod in the namespace
    POD_NAME=$(kubectl get pods -n "$MODEL_NAME" -o jsonpath='{.items[0].metadata.name}')
fi

if [ -z "$POD_NAME" ]; then
    echo -e "${RED}Error: No pods found in namespace $MODEL_NAME${NC}"
    exit 1
fi

echo -e "${GREEN}Using pod: $POD_NAME${NC}"
echo ""

# Warn if rate is too low
if (( $(echo "$RATE <= 1.0" | bc -l) )); then
    echo -e "${YELLOW}Warning: Rate ${RATE} req/s is at or below the SLO threshold of 1 req/s${NC}"
    echo -e "${YELLOW}Error budget will not burn significantly. Recommend rate > 2.0${NC}"
    read -p "Continue anyway? [y/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

# Calculate interval between requests
INTERVAL=$(echo "scale=3; 1.0 / $RATE" | bc)

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Starting error budget burn${NC}"
echo -e "${BLUE}========================================${NC}"
echo "Target rate: ${RATE} req/s"
echo "Interval: ${INTERVAL}s between requests"
echo "Duration: ${DURATION}s"
END_TIME=$(date -d "+${DURATION} seconds" '+%H:%M:%S' 2>/dev/null || date -v+${DURATION}S '+%H:%M:%S')
echo "End time: $END_TIME"
echo ""

# Initialize counters
REQUEST_COUNT=0
ERROR_COUNT=0
START_TIMESTAMP=$(date +%s)
END_TIMESTAMP=$((START_TIMESTAMP + DURATION))

# Trap Ctrl+C
trap 'echo -e "\n${YELLOW}Interrupted by user${NC}"; echo "Sent $REQUEST_COUNT requests before stopping"; exit 0' INT

# Main loop
while [ $(date +%s) -lt $END_TIMESTAMP ]; do
    LOOP_START=$(date +%s.%N)
    
    # Send request
    REQUEST_COUNT=$((REQUEST_COUNT + 1))
    
    HTTP_CODE=$(kubectl exec -n "$MODEL_NAME" "$POD_NAME" -- curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$PROM_URL" 2>/dev/null || echo "000")
    
    if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "302" ]; then
        ERROR_COUNT=$((ERROR_COUNT + 1))
        echo -e "[$(date '+%H:%M:%S')] ${RED}Request #${REQUEST_COUNT} FAILED (status: $HTTP_CODE)${NC}"
    else
        # Print progress every 10 requests
        if [ $((REQUEST_COUNT % 10)) -eq 0 ]; then
            echo -e "[$(date '+%H:%M:%S')] ${GREEN}Sent ${REQUEST_COUNT} requests (errors: ${ERROR_COUNT})${NC}"
        fi
    fi
    
    # Sleep to maintain rate
    LOOP_END=$(date +%s.%N)
    ELAPSED=$(echo "$LOOP_END - $LOOP_START" | bc)
    SLEEP_TIME=$(echo "$INTERVAL - $ELAPSED" | bc)
    
    if (( $(echo "$SLEEP_TIME > 0" | bc -l) )); then
        sleep "$SLEEP_TIME"
    fi
done

# Calculate actual duration
ACTUAL_DURATION=$(($(date +%s) - START_TIMESTAMP))
ACTUAL_RATE=$(echo "scale=2; $REQUEST_COUNT / $ACTUAL_DURATION" | bc)
SUCCESS_COUNT=$((REQUEST_COUNT - ERROR_COUNT))
SUCCESS_RATE=$(echo "scale=1; $SUCCESS_COUNT * 100 / $REQUEST_COUNT" | bc)

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Finished!${NC}"
echo -e "${BLUE}========================================${NC}"
echo "Total requests sent: $REQUEST_COUNT"
echo "Errors: $ERROR_COUNT"
echo "Success rate: ${SUCCESS_RATE}%"
echo "Average rate: ${ACTUAL_RATE} req/s"
echo ""

echo -e "${YELLOW}To check the error budget burn:${NC}"
echo ""
echo "1. Check Sloth metrics in Prometheus:"
echo "   Query: slo:current_burn_rate:ratio{sloth_service='prometheus'}"
echo "   Query: slo:period_error_budget_remaining:ratio{sloth_service='prometheus'}"
echo ""
echo "2. Check for alerts:"
echo "   Query: ALERTS{sloth_service='prometheus'}"
echo ""
echo "3. Access Prometheus UI:"
echo "   juju exec --model $MODEL_NAME --unit ${PROM_APP}/0 -- curl http://localhost:9090"
echo ""
echo "4. View Grafana dashboards if integrated"
echo ""
