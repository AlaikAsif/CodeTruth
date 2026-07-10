export function listUsers(req, res) {
  return res.json(query('all'));
}

export function createUser(req, res) {
  return res.json(query('one'));
}

function query(kind) {
  return { kind };
}

export function neverRegistered(req, res) {
  return res.end();
}
