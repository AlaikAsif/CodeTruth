import { fetchUser } from './api';
import * as fmt from './format';

export function main() {
  const user = fetchUser(1);
  return fmt.pretty(user);
}
