#!/usr/bin/env bash

# Deploy the K8s Dashboard to Cloud Run
cd agent 
docker build -t us-central1-docker.pkg.dev/consumption-442810/micro-service-iam/k8s-agent:v9 . --push
cd ..   
bash deploy-cloudrun.sh

