import { useEffect, useRef } from 'react'

interface Props {
  busy?: boolean
  clearSignal: number
  value: string
  placeholder?: string
  onChange: (text: string) => void
  onSubmit: (text: string) => void
}

export function ProseMirrorComposer({
  busy = false,
  clearSignal,
  value,
  placeholder = '要求后续变更',
  onChange,
  onSubmit,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [clearSignal])

  function submit(): void {
    const currentValue = textareaRef.current?.value ?? value
    onChange(currentValue)
    onSubmit(currentValue)
  }

  return (
    <div className="composer">
      <textarea
        ref={textareaRef}
        className="task-input"
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(event) => onChange(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            submit()
          }
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault()
            submit()
          }
        }}
      />
    </div>
  )
}
