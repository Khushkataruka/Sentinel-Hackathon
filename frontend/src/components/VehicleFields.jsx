// Vehicle attribute vocabularies, shared by the search filters and watchlist entries.

export const COLOURS = ['white', 'black', 'grey', 'silver', 'red', 'blue', 'green', 'yellow', 'brown', 'orange', 'maroon']
export const VTYPES = ['hatchback', 'sedan', 'suv', 'muv', 'pickup', 'van', 'bus', 'truck', 'motorcycle', 'bicycle']
export const MAKES = ['maruti', 'hyundai', 'tata', 'mahindra', 'toyota', 'honda', 'kia']

export function OptionSelect({ label, anyLabel, options, value, onChange }) {
  return (
    <label className="field">
      <span className="data-label">{label}</span>
      <select value={value} onChange={onChange}>
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o.charAt(0).toUpperCase() + o.slice(1)}
          </option>
        ))}
      </select>
    </label>
  )
}
