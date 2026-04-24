# Fy-api overlay: removed @sha256 digest pins so Aliyun container registry
# mirrors (and other mirrors that reject digest pulls) can resolve these
# images via tag. Original upstream digests preserved below for audit:
#   oven/bun:1          → sha256:0733e50325078969732ebe3b15ce4c4be5082f18c4ac1a0f0ca4839c2e4e42a7
#   golang:1.26.1-alpine → sha256:2389ebfa5b7f43eeafbd6be0c3700cc46690ef842ad962f6c5bd6be49ed82039
#   debian:bookworm-slim → sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a
FROM oven/bun:1 AS builder

WORKDIR /build
COPY web/package.json .
COPY web/bun.lock .
RUN bun install
COPY ./web .
COPY ./VERSION .
RUN DISABLE_ESLINT_PLUGIN='true' VITE_REACT_APP_VERSION=$(cat VERSION) bun run build

FROM golang:1.26.1-alpine AS builder2
ENV GO111MODULE=on CGO_ENABLED=0
# Fy-api overlay: use goproxy.cn for in-China build hosts (Aliyun ECS etc.)
# Leaves "direct" as fallback, so upstream private modules still resolve.
ENV GOPROXY=https://goproxy.cn,direct
ENV GOSUMDB=sum.golang.google.cn

ARG TARGETOS
ARG TARGETARCH
ENV GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64}
ENV GOEXPERIMENT=greenteagc

WORKDIR /build

ADD go.mod go.sum ./
RUN go mod download

COPY . .
COPY --from=builder /build/dist ./web/dist
RUN go build -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION)'" -o new-api

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata libasan8 wget \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

COPY --from=builder2 /build/new-api /
EXPOSE 3000
WORKDIR /data
ENTRYPOINT ["/new-api"]
