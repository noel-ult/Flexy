# Build the previously selected AGPL release from its immutable source commit;
# the upstream prebuilt registry images are no longer reliably public.
FROM golang:1.24-bookworm AS build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
ADD https://codeload.github.com/minio/minio/tar.gz/0d7408fc9969caf07de6a8c3a84f9fbb10a6739e /tmp/source.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && go build -trimpath -ldflags='-s -w' -o /out/minio .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data && chown 10001:10001 /data
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/LICENSE /usr/share/minio/LICENSE
COPY --from=build /tmp/source.tar.gz /usr/share/minio/source.tar.gz
USER 10001:10001
ENV HOME=/tmp
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
