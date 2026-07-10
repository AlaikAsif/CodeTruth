import { fetchUser } from '@/api';
import { formatName } from '~lib/format';

export function main() {
  const u = fetchUser(1);
  return formatName(u);
}
