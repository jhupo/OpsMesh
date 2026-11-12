# Build context: an extracted OpsMesh release, never the source checkout.
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --home-dir /var/lib/opsmesh opsmesh
COPY --chown=root:root . /opt/opsmesh/
RUN install -d -m 0755 /etc/opsmesh \
    && install -m 0644 /opt/opsmesh/deploy/host.example.json /etc/opsmesh/host.json
USER opsmesh
WORKDIR /var/lib/opsmesh
EXPOSE 8080
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/opsmesh/opsmesh"]
CMD ["--manifest", "/etc/opsmesh/host.json"]
