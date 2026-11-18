# Build context: an extracted OpsMesh release, never the source checkout.
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --home-dir /var/lib/opsmesh opsmesh
COPY --chown=root:root . /usr/local/lib/opsmesh/
RUN install -d -m 0755 /etc/opsmesh \
    && install -d -m 0700 -o opsmesh -g opsmesh /run/opsmesh \
    && install -m 0644 /usr/local/lib/opsmesh/deploy/host.example.json /etc/opsmesh/host.json \
    && ln -s /usr/local/lib/opsmesh/opsmesh /usr/local/bin/opsmesh
USER opsmesh
WORKDIR /var/lib/opsmesh
EXPOSE 8080
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/opsmesh"]
CMD ["--manifest", "/etc/opsmesh/host.json"]
