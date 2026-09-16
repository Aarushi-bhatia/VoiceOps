import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import AgentDetailPage from './pages/AgentDetailPage'
import AgentsPage from './pages/AgentsPage'
import AnalyticsPage from './pages/AnalyticsPage'
import CallsPage from './pages/CallsPage'
import DashboardPage from './pages/DashboardPage'
import QueuePage from './pages/QueuePage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="calls" element={<CallsPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="agents/:agentId" element={<AgentDetailPage />} />
        <Route path="queue" element={<QueuePage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
