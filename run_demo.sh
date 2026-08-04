#!/usr/bin/env bash

set -e

echo "========================================"
echo " Resetting Minikube"
echo "========================================"

minikube delete
minikube start

echo
echo "========================================"
echo " Building Docker Images"
echo "========================================"

docker build -t customer-ui:demo ./customer-ui
docker build -t orchestrator-svc:demo ./orchestrator-svc
docker build -t mcp-server:demo ./mcp-server
docker build -t analytics-svc:demo ./analytics-svc
docker build -t dashboard-ui:demo ./dashboard-ui

echo
echo "========================================"
echo " Loading Images into Minikube"
echo "========================================"

minikube image load customer-ui:demo
minikube image load orchestrator-svc:demo
minikube image load mcp-server:demo
minikube image load analytics-svc:demo
minikube image load dashboard-ui:demo

echo
echo "Loaded demo images:"
minikube image ls | grep demo

echo
echo "========================================"
echo " Deploying AI Application"
echo "========================================"

make deploy-app

echo
echo "========================================"
echo " Current Pods"
echo "========================================"

kubectl get pods -n default

echo
echo "========================================"
echo " Starting obs-ctl"
echo "========================================"

cd obs-ctl
go run .
