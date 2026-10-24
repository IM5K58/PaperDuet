import { useRef, type MouseEvent, type PointerEvent } from 'react';

/** A backdrop click starts and ends outside the dialog. Padding, scrollbars and
 * dragging a selection out of the dialog keep it open. */
export function useDialogDismissal(onClose: () => void) {
  const startedOutside = useRef(false);
  const outside = (event: MouseEvent<HTMLDialogElement> | PointerEvent<HTMLDialogElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return event.target === event.currentTarget &&
      (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom);
  };
  return {
    onPointerDown: (event: PointerEvent<HTMLDialogElement>) => { startedOutside.current = event.button === 0 && outside(event); },
    onPointerCancel: () => { startedOutside.current = false; },
    onClick: (event: MouseEvent<HTMLDialogElement>) => {
      const dismiss = startedOutside.current && outside(event);
      startedOutside.current = false;
      if (dismiss) onClose();
    },
  };
}
