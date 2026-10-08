FROM golang:1.24-bookworm AS build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
ADD https://codeload.github.com/minio/mc/tar.gz/b00526b153a31b36767991a4f5ce2cced435ee8e /tmp/source.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && go build -trimpath -ldflags='-s -w' -o /out/mc .

# A shell is required for the one-shot private bucket initialization command.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/mc /usr/local/bin/mc
COPY --from=build /src/LICENSE /usr/share/minio-client/LICENSE
COPY --from=build /tmp/source.tar.gz /usr/share/minio-client/source.tar.gz
USER 10001:10001
ENV HOME=/tmp
ENTRYPOINT ["mc"]
