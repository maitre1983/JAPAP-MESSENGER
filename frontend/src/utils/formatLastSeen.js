/**
 * iter237u — formatLastSeen
 *
 * Returns a French label like "Vu il y a 14 min", "Vu hier à 14h32",
 * "Vu lundi à 14h32" or "Vu le 5 mai à 14h32" depending on how recent
 * the timestamp is. Falls back to "Hors ligne" if the value is missing
 * or unparseable.
 *
 * Used in the chat header presence line. The thresholds match what
 * users intuitively expect on WhatsApp/Telegram.
 */
const WEEKDAYS = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
const MONTHS = [
  'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre',
];

function pad(n) { return n < 10 ? `0${n}` : `${n}`; }

function timeOfDay(d) {
  // "14h32" — French clock format with hour separator.
  return `${d.getHours()}h${pad(d.getMinutes())}`;
}

export default function formatLastSeen(iso) {
  if (!iso) return 'Hors ligne';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Hors ligne';
  const now = new Date();
  const diffMs = now - d;
  const diffMin = Math.floor(diffMs / 60000);

  // < 1 min
  if (diffMin < 1) return "Vu à l'instant";
  // < 1 hour
  if (diffMin < 60) return `Vu il y a ${diffMin} min`;

  // Calculate calendar-day delta (today / yesterday / this week / older).
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const target = new Date(d);
  target.setHours(0, 0, 0, 0);
  const dayDiff = Math.round((today - target) / 86400000);

  if (dayDiff === 0) return `Vu aujourd'hui à ${timeOfDay(d)}`;
  if (dayDiff === 1) return `Vu hier à ${timeOfDay(d)}`;
  if (dayDiff > 1 && dayDiff < 7) {
    return `Vu ${WEEKDAYS[d.getDay()]} à ${timeOfDay(d)}`;
  }
  // Older — give a calendar date (with year only if not current).
  const sameYear = d.getFullYear() === now.getFullYear();
  const datePart = sameYear
    ? `le ${d.getDate()} ${MONTHS[d.getMonth()]}`
    : `le ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  return `Vu ${datePart} à ${timeOfDay(d)}`;
}
