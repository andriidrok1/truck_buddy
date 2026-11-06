#!/usr/bin/env bash
# Lightsail user-data: Docker + compose plugin, /opt/truckbuddy owned by ubuntu.
# Lightsail runs user-data under sh after its own preamble: keep this POSIX.
set -eux
apt-get update
apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu
mkdir -p /opt/truckbuddy
chown ubuntu:ubuntu /opt/truckbuddy
