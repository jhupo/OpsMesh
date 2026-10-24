#!/bin/sh
# Download a pinned Linux release, verify its checksum, then invoke its installer.
set -eu

version=${1:?Usage: install.sh vMAJOR.MINOR.PATCH --config /path/to/install.env [installer options]}
shift
printf '%s\n' "$version" | awk '/^v[0-9]+\.[0-9]+\.[0-9]+$/ {ok=1} END {exit !ok}' || {
  echo 'A pinned vMAJOR.MINOR.PATCH version is required' >&2; exit 2
}
repository=${OPSMESH_REPOSITORY:-jhupo/OpsMesh}
case "$repository" in
  */*) ;;
  *) echo 'OPSMESH_REPOSITORY must be owner/repository' >&2; exit 2 ;;
esac
case "$repository" in *[!a-zA-Z0-9_./-]*) echo 'Invalid repository' >&2; exit 2 ;; esac
[ "$(uname -s)" = Linux ] || { echo 'Only Linux is supported' >&2; exit 2; }
case "$(uname -m)" in
  x86_64) architecture=x86_64 ;;
  aarch64|arm64) architecture=aarch64 ;;
  *) echo 'Only x86_64 and aarch64 are supported' >&2; exit 2 ;;
esac
for command in curl sha256sum tar mktemp; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 2; }
done
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT HUP INT TERM
asset="opsmesh-$version-linux-$architecture.tar.gz"
base="https://github.com/$repository/releases/download/$version"
curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 "$base/$asset" -o "$temporary/$asset"
curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 "$base/SHA256SUMS" -o "$temporary/SHA256SUMS.all"
awk -v name="$asset" '$2 == name {print}' "$temporary/SHA256SUMS.all" > "$temporary/SHA256SUMS"
[ "$(wc -l < "$temporary/SHA256SUMS")" -eq 1 ] || { echo 'Missing or duplicate checksum' >&2; exit 1; }
(cd "$temporary" && sha256sum -c SHA256SUMS)
tar -xzf "$temporary/$asset" -C "$temporary"
"$temporary/opsmesh/bin/opsmesh" install --repository "$repository" "$@"
