FROM node:22-slim AS build
WORKDIR /srv
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM node:22-slim
WORKDIR /srv
COPY --from=build /srv/.next/standalone ./
COPY --from=build /srv/.next/static ./.next/static
ENV HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["node", "server.js"]
