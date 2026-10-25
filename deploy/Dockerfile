FROM ubuntu:26.04
ARG TARGETARCH
ARG VERSION
ARG REVISION
ARG SOURCE_URL=https://github.com/jhupo/OpsMesh
LABEL org.opencontainers.image.title="OpsMesh" \
      org.opencontainers.image.description="Agent plugin application host" \
      org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ca-certificates libstdc++6 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home opsmesh
WORKDIR /app
COPY dist/linux-${TARGETARCH}/opsmesh/ /opt/opsmesh/
RUN mkdir -p /app/data && chown opsmesh:opsmesh /app/data
ENV OPSMESH_DATA_DIR=/app/data
EXPOSE 8000
USER opsmesh
ENTRYPOINT ["/opt/opsmesh/bin/opsmesh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
