# Caddy with the Route 53 DNS-01 module, for the origin certificate.
#
# WHY A CUSTOM BUILD
# ------------------
# The stock `caddy` image cannot do DNS-01 against Route 53. HTTP-01 would
# need port 80 open to the world on the origin host, which the architecture
# refuses (the security group admits only CloudFront's origin-facing ranges
# on 443). DNS-01 needs no inbound port at all: Caddy writes one TXT record,
# under an IAM policy that permits exactly that record and nothing else
# (amendment C3; infra/terraform/stack/containers.tf `caddy_dns01`).
#
# Pin both tags before the first build and record the digests in the
# release manifest; a floating `caddy:2` is the same problem as `latest`.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/route53

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
