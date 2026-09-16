import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

const LIVE = 5000 // queue and in-flight views need to feel live

export const useHealth = () => useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15000 })

export const useAgents = (params) =>
  useQuery({ queryKey: ['agents', params], queryFn: () => api.listAgents(params) })

export const useAgent = (id) =>
  useQuery({ queryKey: ['agent', id], queryFn: () => api.getAgent(id), enabled: Boolean(id) })

export const useAgentStats = (id) =>
  useQuery({ queryKey: ['agent-stats', id], queryFn: () => api.agentStats(id), enabled: Boolean(id) })

export const useCalls = (params) =>
  useQuery({ queryKey: ['calls', params], queryFn: () => api.listCalls(params), refetchInterval: LIVE })

export const useCall = (id) =>
  useQuery({ queryKey: ['call', id], queryFn: () => api.getCall(id), enabled: Boolean(id) })

export const useQueueStats = () =>
  useQuery({ queryKey: ['queue-stats'], queryFn: api.queueStats, refetchInterval: LIVE })

export const useDeadLetter = (params) =>
  useQuery({ queryKey: ['dead-letter', params], queryFn: () => api.deadLetter(params), refetchInterval: LIVE })

export const useOverview = (days) =>
  useQuery({ queryKey: ['overview', days], queryFn: () => api.overview({ days }) })

export const useTimeseries = (days, bucket = 'day') =>
  useQuery({ queryKey: ['timeseries', days, bucket], queryFn: () => api.timeseries({ days, bucket }) })

export const useAgentPerformance = (days) =>
  useQuery({ queryKey: ['agent-performance', days], queryFn: () => api.agentPerformance({ days }) })

/** Mutations invalidate the views their change is visible in. */
export function useAgentMutations() {
  const client = useQueryClient()
  const touch = () => {
    client.invalidateQueries({ queryKey: ['agents'] })
    client.invalidateQueries({ queryKey: ['agent-performance'] })
  }
  return {
    create: useMutation({ mutationFn: api.createAgent, onSuccess: touch }),
    update: useMutation({
      mutationFn: ({ id, body }) => api.updateAgent(id, body),
      onSuccess: (_data, variables) => {
        touch()
        client.invalidateQueries({ queryKey: ['agent', variables.id] })
      },
    }),
    remove: useMutation({ mutationFn: api.deleteAgent, onSuccess: touch }),
    simulate: useMutation({ mutationFn: ({ id, body }) => api.simulateAgent(id, body) }),
  }
}

export function useCallMutations() {
  const client = useQueryClient()
  const touch = () => {
    client.invalidateQueries({ queryKey: ['calls'] })
    client.invalidateQueries({ queryKey: ['queue-stats'] })
    client.invalidateQueries({ queryKey: ['dead-letter'] })
    client.invalidateQueries({ queryKey: ['overview'] })
  }
  return {
    create: useMutation({ mutationFn: api.createCall, onSuccess: touch }),
    createBulk: useMutation({ mutationFn: api.createCallsBulk, onSuccess: touch }),
    cancel: useMutation({ mutationFn: api.cancelCall, onSuccess: touch }),
    retry: useMutation({ mutationFn: api.retryCall, onSuccess: touch }),
    requeue: useMutation({ mutationFn: api.requeueDeadLetter, onSuccess: touch }),
    purge: useMutation({ mutationFn: api.purgeDeadLetter, onSuccess: touch }),
  }
}
