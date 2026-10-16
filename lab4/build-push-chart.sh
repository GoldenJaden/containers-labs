#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
chart_dir="$script_dir/helm/lab4-app"
endpoint="http://192.168.0.242:3000/api/packages/gjaden/helm/api/charts"

: "${user:?Set the user environment variable}"
: "${password:?Set the password environment variable}"
command -v helm >/dev/null || { echo 'helm is required' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl is required' >&2; exit 1; }

helm lint "$chart_dir"
package_dir="$(mktemp -d)"
helm package "$chart_dir" --destination "$package_dir"
packages=("$package_dir"/*.tgz)
[[ ${#packages[@]} -eq 1 && -f "${packages[0]}" ]] || {
  echo 'Expected exactly one packaged chart' >&2
  exit 1
}

echo "Uploading ${packages[0]}"
curl --fail-with-body --show-error --silent \
  --user "$user:$password" \
  -X POST \
  --upload-file "${packages[0]}" \
  "$endpoint"
printf '\nChart package retained at %s\n' "${packages[0]}"
