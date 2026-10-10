FROM node:20-alpine
WORKDIR /app
COPY server.js ./
EXPOSE 5678
ENV PORT=5678 TZ=Europe/Moscow
CMD ["node", "server.js"]
