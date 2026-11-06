#!/usr/bin/env bash
# Run ON THE MAC, once. Creates the truckbuddy-api Lightsail instance, a static
# IP and the 22/80/443 firewall. Touches nothing that is not named truckbuddy-*.
# Needs an AWS profile with Lightsail rights:  AWS_PROFILE=<profile> bash provision.sh
set -euo pipefail
REGION="${REGION:-us-east-1}"          # same region as VeteranHQ
ZONE="${ZONE:-${REGION}a}"
NAME=truckbuddy-api
IPNAME=truckbuddy-api-ip
KEYNAME=truckbuddy-api-key
KEYFILE="$HOME/.ssh/truckbuddy-aws.pem"
LS="aws lightsail --region $REGION"

# Smallest Linux bundle with >= 2 GB RAM, and the newest Ubuntu LTS blueprint.
BUNDLE=$($LS get-bundles --query "sort_by(bundles[?isActive && ramSizeInGb>=\`2\` && contains(supportedPlatforms,'LINUX_UNIX') && !contains(bundleId,'ipv6')], &price)[0].bundleId" --output text)
BLUEPRINT=$($LS get-blueprints --query "sort_by(blueprints[?platform=='LINUX_UNIX' && starts_with(blueprintId,'ubuntu_') && isActive], &blueprintId)[-1].blueprintId" --output text)
echo "bundle=$BUNDLE blueprint=$BLUEPRINT zone=$ZONE"

if [ ! -f "$KEYFILE" ]; then
  umask 077
  $LS create-key-pair --key-pair-name "$KEYNAME" --query privateKeyBase64 --output text > "$KEYFILE"
  chmod 600 "$KEYFILE"
fi

$LS get-instance --instance-name "$NAME" >/dev/null 2>&1 || \
  $LS create-instances --instance-names "$NAME" --availability-zone "$ZONE" \
    --blueprint-id "$BLUEPRINT" --bundle-id "$BUNDLE" --key-pair-name "$KEYNAME" \
    --user-data "$(cat "$(dirname "$0")/bootstrap.sh")" --tags key=project,value=truckbuddy >/dev/null

until [ "$($LS get-instance --instance-name "$NAME" --query instance.state.name --output text)" = running ]; do sleep 5; done

$LS get-static-ip --static-ip-name "$IPNAME" >/dev/null 2>&1 || $LS allocate-static-ip --static-ip-name "$IPNAME" >/dev/null
$LS attach-static-ip --static-ip-name "$IPNAME" --instance-name "$NAME" >/dev/null || true

$LS put-instance-public-ports --instance-name "$NAME" --port-infos \
  fromPort=22,toPort=22,protocol=tcp fromPort=80,toPort=80,protocol=tcp fromPort=443,toPort=443,protocol=tcp >/dev/null

IP=$($LS get-static-ip --static-ip-name "$IPNAME" --query staticIp.ipAddress --output text)
echo "instance=$NAME region=$REGION static_ip=$IP key=$KEYFILE"
echo "DNS: A  api.truckbuddy.io  ->  $IP"
