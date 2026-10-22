import { Link } from 'react-router'
import PageSection from '../components/PageSection'

export default function NotFoundPage() {
  return (
    <PageSection title="Page not found" description="The requested page could not be found.">
      <Link to="/dashboard" className="btn btn-primary">Back to dashboard</Link>
    </PageSection>
  )
}
