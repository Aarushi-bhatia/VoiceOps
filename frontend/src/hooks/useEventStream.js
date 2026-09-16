import { useEffect, useRef, useState } from 'react'
import { eventStreamUrl } from '../lib/api'

/**
 * Subscribes to the worker event stream.
 *
 * Reconnects with exponential backoff, mirroring the retry policy the backend
 * applies to calls, so a restarting API does not leave the dashboard dead.
 */
export function useEventStream({ limit = 50, enabled = true } = {}) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const socketRef = useRef(null)
  const attemptRef = useRef(0)

  useEffect(() => {
    if (!enabled) return undefined
    let closed = false
    let timer

    const connect = () => {
      if (closed) return
      let socket
      try {
        socket = new WebSocket(eventStreamUrl())
      } catch {
        scheduleReconnect()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        attemptRef.current = 0
        setConnected(true)
      }
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data)
          setEvents((current) => [{ ...event, key: `${event.ts}-${Math.random()}` }, ...current].slice(0, limit))
        } catch {
          // A malformed frame should never break the feed.
        }
      }
      socket.onclose = () => {
        setConnected(false)
        scheduleReconnect()
      }
      socket.onerror = () => socket.close()
    }

    const scheduleReconnect = () => {
      if (closed) return
      const attempt = (attemptRef.current += 1)
      const delay = Math.min(1000 * 2 ** (attempt - 1), 30000)
      timer = setTimeout(connect, delay)
    }

    connect()
    return () => {
      closed = true
      clearTimeout(timer)
      socketRef.current?.close()
    }
  }, [enabled, limit])

  return { events, connected, clear: () => setEvents([]) }
}
