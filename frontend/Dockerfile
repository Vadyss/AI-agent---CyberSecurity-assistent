# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
FROM node:22-alpine AS build
LABEL org.opencontainers.image.authors="Adam Krúpa, Ondra Csajka"
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
RUN npm run build

FROM nginxinc/nginx-unprivileged:alpine
LABEL org.opencontainers.image.authors="Adam Krúpa, Ondra Csajka"
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 8080
