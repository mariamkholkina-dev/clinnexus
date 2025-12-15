FROM node:20-alpine

WORKDIR /app

ENV NODE_ENV=development

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--port", "3000", "--hostname", "0.0.0.0"]


