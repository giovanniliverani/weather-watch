// Interface icons, drawn as SVG in one stroke weight; they take the text colour of their button.

export function Chevron() {
  return (
    <svg className="chevron" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function Close() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  )
}
