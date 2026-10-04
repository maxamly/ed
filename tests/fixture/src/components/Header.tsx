import { formatDate } from '@/utils/date'

export function Header({ date }: { date: Date }) {
  console.log('render header')
  return <h1>{formatDate(date)}</h1>
}
