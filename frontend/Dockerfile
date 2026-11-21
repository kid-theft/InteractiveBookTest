FROM node:22-alpine
WORKDIR /web
COPY package.json ./package.json
COPY package-lock.json ./package-lock.json
RUN npm ci
COPY . .
COPY docker-entrypoint.sh /usr/local/bin/project-entrypoint
RUN chmod +x /usr/local/bin/project-entrypoint
ENTRYPOINT ["/usr/local/bin/project-entrypoint"]
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
