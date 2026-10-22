type EmptyPanelProps = {
  title: string
  description: string
}

export default function EmptyPanel({ title, description }: EmptyPanelProps) {
  return (
    <div className="card border border-base-300 bg-base-100">
      <div className="card-body">
        <h2 className="card-title text-lg">{title}</h2>
        <p className="text-sm text-base-content/60">{description}</p>
      </div>
    </div>
  )
}
