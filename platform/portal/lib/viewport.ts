// The one phone breakpoint, shared with the CSS (`@media (max-width: 760px)`): the sidebar becomes a drawer and the
// side pane covers the chat. Scripts ask the same media query rather than comparing innerWidth, so 760px is a phone
// in both.
export const PHONE_QUERY = "(max-width: 760px)";

export function isPhone(): boolean {
  return window.matchMedia(PHONE_QUERY).matches;
}
