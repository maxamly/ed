export function Modal({ open, title }: { open: boolean; title: string }) {
  return open ? <div role="dialog">{title}</div> : null
}
