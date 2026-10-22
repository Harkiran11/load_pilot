import { Navigate, Route, Routes } from 'react-router'
import AppLayout from './layouts/AppLayout'
import DashboardPage from './pages/DashboardPage'
import AnalyticsPage from './pages/AnalyticsPage'
import LoadBoardPage from './pages/LoadBoardPage'
import BidLogsPage from './pages/BidLogsPage'
import TerminalsPage from './pages/TerminalsPage'
import FleetPage from './pages/FleetPage'
import ShipmentsPage from './pages/ShipmentsPage'
import ActivityPage from './pages/ActivityPage'
import RecordsPage from './pages/RecordsPage'
import SettingsPage from './pages/SettingsPage'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="loads" element={<LoadBoardPage />} />
        <Route path="bids" element={<BidLogsPage />} />
        <Route path="terminals" element={<TerminalsPage />} />
        <Route path="fleet" element={<FleetPage />} />
        <Route path="shipments" element={<ShipmentsPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="records" element={<RecordsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
