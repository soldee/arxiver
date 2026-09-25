FROM node:alpine3.24 AS build

WORKDIR /app/src
COPY src/frontend/package*.json .
RUN npm ci
COPY src/frontend .
RUN npm run build

FROM nginx:stable-alpine
COPY --from=build /app/src/dist /usr/share/nginx/html
COPY docker/nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
