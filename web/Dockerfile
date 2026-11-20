# Stage 1: build the React app into static files
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: serve those files with nginx. Node isn't in the final image at all,
# which keeps it small (~50 MB instead of ~500 MB).
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
