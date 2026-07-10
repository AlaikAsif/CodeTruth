import { UserCard } from './UserCard';

export function App() {
  return (
    <div>
      <UserCard onClick={handleClick} />
      <LocalBadge />
    </div>
  );
}

function handleClick() {
  return 'clicked';
}

function LocalBadge() {
  return <span>badge</span>;
}

function UnusedWidget() {
  return null;
}
