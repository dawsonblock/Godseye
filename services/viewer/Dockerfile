FROM node:20-alpine

WORKDIR /app

COPY services/viewer/package.json ./
RUN npm install

COPY services/viewer ./

# Expose Vite dev server port
EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
