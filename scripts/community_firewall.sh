#!/bin/sh
# Federation Standard v1 §2: host firewall for the community router's bridge (generic example).
# Run as root on the Docker host after the `community` network exists; idempotent.
# New connections from the bridge may go to the internet only: RFC 1918, CGNAT, link-local
# (cloud metadata), IPv6 ULA and link-local are dropped in DOCKER-USER, and the host itself
# (gateway, Docker API, SSH) in INPUT. Replies to inbound proxy traffic stay allowed.
set -eu
BRIDGE="${1:-gf-community}"

apply() { # $1 = iptables|ip6tables, rest = blocked destination prefixes
    ipt="$1"
    shift
    hook=DOCKER-USER
    "$ipt" -n -L DOCKER-USER >/dev/null 2>&1 || hook=FORWARD
    "$ipt" -N GF-COMMUNITY 2>/dev/null || "$ipt" -F GF-COMMUNITY
    "$ipt" -A GF-COMMUNITY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    # Public URLs of a reverse proxy on this host hairpin back as DNAT to its container.
    "$ipt" -A GF-COMMUNITY -p tcp -m conntrack --ctstate DNAT --ctorigdstport 443 -j RETURN
    for prefix in "$@"; do
        "$ipt" -A GF-COMMUNITY -d "$prefix" -j DROP
    done
    "$ipt" -C "$hook" -i "$BRIDGE" -j GF-COMMUNITY 2>/dev/null ||
        "$ipt" -I "$hook" -i "$BRIDGE" -j GF-COMMUNITY
    "$ipt" -C INPUT -i "$BRIDGE" -m conntrack --ctstate NEW -j DROP 2>/dev/null ||
        "$ipt" -I INPUT -i "$BRIDGE" -m conntrack --ctstate NEW -j DROP
}

apply iptables 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16
if command -v ip6tables >/dev/null 2>&1; then
    apply ip6tables fc00::/7 fe80::/10
fi
