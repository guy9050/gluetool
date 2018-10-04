Cache Protocol
==============

Modules may provide a cache other modules could use to store data temporarily, possibly with persistency across multiple runs of ``gluetool`` pipelines.


Query
-----

None, often the shared function providing access to cache interface bears name ``cache``.


Packet
------

.. py:method:: ``get(key: str, default: Any=None) -> Any``

   Return a value of key ``key`` or return value of ``default`` if the key does not exist.


.. py:method:: ``gets(key: str, default: Any=None, cas_default: Any=None) -> Tuple(Any, Any)``

   Return a tuple consiting of value of key ``key`` and CAS tag, or tuple of ``default`` and ``cas_default`` if the key does not exist.


.. py:method:: ``set(key: str, value: Any) -> Any``

   Set a value of key ``key`` to ``value``.


.. py:method:: ``cas(key: str, value: str, tag: Any) -> Any``

   *Check And Set* operation. Set a value of key ``key`` to ``value``. ``tag`` is the *CAS tag* previously obtained by calling ``gets``. If the key was modified by other process/thread between ``gets`` and ``cas``, the update **is not** performed and the method returns ``False``. In such case, to successfully change the value, one must call ``gets`` again to obtain changed value and CAS tag, and pass new CAS tag to ``cas`` method.
   If the value was not changed between ``gets`` and ``cas`` calls, ``cas`` return ``True``.
