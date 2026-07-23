# Stage 1: Build the application
FROM node:20-slim AS build
WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine
# Copy the built assets to Nginx's public directory
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Expose port 80 (Standard for web containers)
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
