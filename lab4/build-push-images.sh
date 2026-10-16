#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
registry="192.168.0.242:3000"

if (( $# > 1 )); then
  echo "Usage: $0 [tag]" >&2
  exit 1
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: $0 [tag]"
  echo 'Use one tag for all images; omit it to use the chart tags.'
  exit 0
fi
if (( $# == 1 )) && [[ ! "$1" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  echo "Invalid image tag: $1" >&2
  exit 1
fi

command -v docker >/dev/null || { echo 'docker is required' >&2; exit 1; }
command -v helm >/dev/null || { echo 'helm is required' >&2; exit 1; }

# Read the actual image names and tags used by the chart.
manifest="$(helm template image-build "$script_dir/helm/lab4-app" --show-only templates/deployment.yaml)"
images=()
for component in api worker batch; do
  image="$(awk -v repository="$registry/gjaden/lab4-$component:" '
    $1 == "image:" {
      gsub(/"/, "", $2)
      if (index($2, repository) == 1) print $2
    }
  ' <<< "$manifest")"
  if [[ -z "$image" || "$image" == *$'\n'* ]]; then
    echo "Expected exactly one image for $component in $registry/gjaden" >&2
    exit 1
  fi
  if (( $# == 1 )); then
    image="${image%:*}:$1"
  fi
  images+=("$image")
  echo "Building $image"
  docker build --tag "$image" "$script_dir/$component"
done

# Push only after every build has succeeded.
for image in "${images[@]}"; do
  echo "Pushing $image"
  docker push "$image"
done
