# The stack

Camayoc is the layer between raw activity and the governed store: it owns
what the knots mean and how knowledge earns its way into the graph.

- [quipu](https://github.com/scbrown/quipu) stores and governs, and holds no extraction and no LLM.
- [bobbin](https://github.com/scbrown/bobbin) serves knowledge back into agent context.
- [yupana](https://github.com/scbrown/yupana) observes code structure.
- [desire-path](https://github.com/scbrown/desire-path) records the tool calls agents get wrong.
- [tambo](https://github.com/intigraph/tambo) installs all of them together and proves each one works.

The diagram and the division of labour are on [Why camayoc](why-camayoc.md#role-in-the-stack).
