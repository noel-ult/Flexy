FROM node:22-bookworm-slim AS build

WORKDIR /srv/flexy/web

COPY web/package.json ./
# Direct dependency versions are fixed in package.json.  A committed lockfile
# should replace this with `npm ci` once dependency metadata is available to
# generate and review one.  Do not contact audit/funding endpoints while
# building a production image.
RUN npm install --ignore-scripts --no-audit --no-fund

COPY web ./
ARG NEXT_PUBLIC_API_BASE_URL=
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run build

FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /srv/flexy/web

RUN groupadd --gid 10001 flexy \
    && useradd --uid 10001 --gid flexy --create-home --shell /usr/sbin/nologin flexy

COPY --from=build --chown=10001:10001 /srv/flexy/web ./

USER 10001:10001

EXPOSE 3000
CMD ["npm", "start"]
