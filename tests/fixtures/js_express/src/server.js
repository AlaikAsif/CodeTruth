import express from 'express';
import { listUsers, createUser } from './handlers';

const app = express();

app.get('/users', listUsers);
app.post('/users', createUser);

app.listen(3000, onReady);

function onReady() {
  return 'server up';
}
