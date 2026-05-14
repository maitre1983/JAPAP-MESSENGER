// iter240j — Wrapper universel pour rendre cliquable un nom d'utilisateur
// dans le Feed, Chat, Marketplace, Crowdfunding, Reels, Stories, etc.
// Navigation vers /profile/:username (préféré) ou /profile/:user_id en
// fallback. ADDITIF : remplace simplement un <span>/<a> existant — la
// logique parent ne change pas.
import { useNavigate } from 'react-router-dom';

export default function UserNameLink({
  user,                 // { user_id, username, ... } OR
  username,             // explicit username (preferred)
  userId,               // explicit user_id (fallback)
  children,             // displayed text
  className = '',
  style = {},
  stopPropagation = true,
  'data-testid': testId,
}) {
  const navigate = useNavigate();
  const handle = username || user?.username || userId || user?.user_id;
  if (!handle) return <span className={className} style={style}>{children}</span>;
  const onClick = (e) => {
    if (stopPropagation) e.stopPropagation();
    navigate(`/profile/${encodeURIComponent(handle)}`);
  };
  return (
    <span
      onClick={onClick}
      role="link"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onClick(e); }}
      data-testid={testId || 'user-name-link'}
      className={className}
      style={{
        cursor: 'pointer',
        color: 'inherit',
        fontWeight: 600,
        textUnderlineOffset: '2px',
        ...style,
      }}>
      {children}
    </span>
  );
}
