# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the image.
RUN npm run build

# Production stage
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 80

# The CSP's connect-src, substituted at container start, not build time.
ENV NOTES_BASE_URL=https://notes.geckopico.com

# Only this one is substituted; nginx's own $variables ($uri, $host …) must survive.
CMD ["/bin/sh", "-c", "envsubst '${NOTES_BASE_URL}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
