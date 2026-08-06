#!/usr/bin/env bash

set -e

echo "========================================"
echo " Resetting Minikube"
echo "========================================"

minikube delete
minikube start
minikube addons enable ingress

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
echo " Demo Setup Complete"
echo "========================================"
echo
echo "Before launching obs-ctl:"
echo "1. In a new terminal run:"
echo "     minikube tunnel"
echo
echo "2. Ensure /etc/hosts contains:"
echo "     127.0.0.1 dashboard.local"
echo "     127.0.0.1 customer.local"
echo
read -p "Press Enter after the tunnel is running..."

echo
echo "========================================"
echo " Starting obs-ctl"
echo "========================================"

cd obs-ctl
go run .