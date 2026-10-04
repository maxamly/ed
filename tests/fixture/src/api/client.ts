import { formatDate, parseDate } from '@/utils/date'

export async function getJson(url: string) {
  console.log('GET', url)
  const res = await fetch(url)
  return res.json()
}

function legacyFetch(url: string) {
  return fetch(url).then((r) => {
    return r.json()
  })
}

export function stamp(s: string) {
  console.log('stamp', s) // @debug
  return formatDate(parseDate(s))
}
