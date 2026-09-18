# SwingTrader frontend image: multi-stage build -> static nginx serve.
#
# Build context is frontend/:
#
#   docker build -t swingtrader-frontend frontend/
#
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
