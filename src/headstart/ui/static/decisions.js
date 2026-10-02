/* One user decision, with visible context and native dialog focus/keyboard behavior.
   request resolves a value only after confirmation. Cancelling never invokes a write. */
globalThis.HeadStartDecision = {
  request({ title, body = '', label = null, value = '', confirm = 'Confirm', danger = false }) {
    const get = id => document.getElementById(id);
    const dialog = get('decision-dialog'), input = get('decision-value');
    get('decision-title').textContent = title;
    get('decision-body').textContent = body;
    get('decision-field').hidden = !label;
    get('decision-label').textContent = label || '';
    input.value = value; input.required = !!label;
    const button = get('decision-confirm');
    button.textContent = confirm; button.classList.toggle('danger', danger);
    dialog.returnValue = 'cancel';
    return new Promise(resolve => {
      dialog.addEventListener('close', () => resolve({ confirmed: dialog.returnValue === 'confirm', value: input.value.trim() }), { once: true });
      dialog.showModal();
      if (label) { input.focus(); input.select(); }
      else dialog.querySelector('button[value="cancel"]').focus();
    });
  },
};
