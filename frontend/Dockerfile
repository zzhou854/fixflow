# syntax=docker/dockerfile:1.7
FROM node:24.4.1-alpine3.22 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=http://127.0.0.1:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM node:24.4.1-alpine3.22 AS runtime
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=5173
WORKDIR /app
COPY --from=build --chown=node:node /app/dist ./dist
COPY --chown=node:node server.mjs ./
USER node
EXPOSE 5173
CMD ["node", "server.mjs"]
