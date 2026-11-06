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
# Copied as is: nothing in it is substituted any more. The Notes origin in connect-src
# (NOTES_BASE_URL) went when the suite started signing people in on its own pages.
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
